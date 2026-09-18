"""Supervise the frozen Case118 S5 annual shard schedule."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4b import (
    ANNUAL_SCOPE,
    ProcessObservation,
    _outer,
    _terminate,
    process_observations,
    process_tree_usage,
)
from experiments.case118_annual_hierarchy.s4b_execution import (
    audit_shard,
    merge_shard_summaries,
    shard_entry,
)
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    object_sha256,
)
from experiments.case118_annual_hierarchy.s5_execution import (
    AGGREGATE_RSS_LIMIT_MIB,
    ANNUAL_SHARD_IDS,
    ANNUAL_WAVES,
    DEFAULT_NUMERICAL_AUTHORITY_PATH,
    PER_WORKER_RSS_LIMIT_MIB,
    SCHEMA_VERSION,
    annual_registry,
    execution_context,
    load_numerical_authority,
    wave_index_for_request,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)
from experiments.case118_annual_hierarchy.s5_source_transition import (
    historical_provenance_matches,
    load_transition,
    publish_transition,
    require_current_transition,
    transition_context_authority_pairs,
)


DEFAULT_OUTPUT_ROOT = (
    ROOT / "experiments/case118_annual_hierarchy/results/s4b_annual_ac"
)
POLL_SECONDS = 1.0
WORKER_TERMINATION_GRACE_SECONDS = 30.0


class _SupervisorInterrupted(BaseException):
    pass


def _annual_registry_sha256() -> str:
    return str(annual_registry()["registry_sha256"])


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def _shard_directory(shard_id: str, output_root: Path) -> Path:
    _, shard = shard_entry(shard_id)
    location = Path(
        str(_mapping(shard["locations"], "shard locations")["output_directory"])
    )
    expected = DEFAULT_OUTPUT_ROOT / f"shard-{int(shard_id[-3:]):03d}"
    if ROOT / location != expected:
        raise ValueError("S5 manifest shard output location drifted")
    return output_root / expected.name


def _supervision_paths(output_root: Path, wave_index: int) -> list[Path]:
    return sorted(output_root.glob(f"supervision-wave-{wave_index:03d}-*.json"))


def _next_supervision_path(output_root: Path, wave_index: int) -> Path:
    return output_root / (
        f"supervision-wave-{wave_index:03d}-"
        f"{len(_supervision_paths(output_root, wave_index)):03d}.json"
    )


def _audited_completed_worker(
    directory: Path,
    shard_id: str,
    context: Mapping[str, object],
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Keep worker metadata separate from the hash-bound scientific summary."""
    from experiments.case118_annual_hierarchy.s5_source_transition import (
        worker_source_matches,
    )

    worker = _mapping(
        json.loads((directory / "shard-result.json").read_text()), "S5 worker"
    )
    _, shard = shard_entry(shard_id)
    summary = audit_shard(
        directory,
        shard=shard,
        outer=_outer(),
        expected_execution_registry_sha256=_annual_registry_sha256(),
        allowed_execution_modes=(ANNUAL_SCOPE,),
    )
    transition = load_transition(directory.parent)
    allowed_contexts = tuple(
        item[0] for item in transition_context_authority_pairs(transition)
    ) or (context,)
    worker_context = _mapping(
        worker.get("execution_context"), "completed worker context"
    )
    if (
        summary.get("classification") != "accepted"
        or summary.get("execution_complete") is not True
        or summary.get("all_independent_audits_agree") is not True
        or any(worker.get(name) != value for name, value in summary.items())
        or worker_context not in allowed_contexts
        or not worker_source_matches(directory, worker, worker_context, transition)
        or worker.get("execution_mode") != ANNUAL_SCOPE
    ):
        raise ValueError(
            "S5 worker disagrees with independent shard audit or provenance"
        )
    return worker, summary


def _validate_completed_prefix(
    output_root: Path,
    next_wave: object,
    context: Mapping[str, object],
    authority: Mapping[str, object],
    *,
    use_completed_prefix_anchor: bool = False,
) -> None:
    """Audit the retained prefix and completed peers before permitting more work."""
    if (
        isinstance(next_wave, bool)
        or not isinstance(next_wave, int)
        or not 0 <= next_wave <= len(ANNUAL_WAVES)
    ):
        raise ValueError("S5 next-wave coordinate is invalid")
    required = {item for wave in ANNUAL_WAVES[:next_wave] for item in wave}
    allowed = required | (
        set(ANNUAL_WAVES[next_wave]) if next_wave < len(ANNUAL_WAVES) else set()
    )
    completed = set(_completed_shards(output_root))
    if not required <= completed or not completed <= allowed:
        raise ValueError("S5 retained completed prefix violates the wave schedule")
    anchored: frozenset[str] = frozenset()
    if use_completed_prefix_anchor and next_wave >= 5:
        from experiments.case118_annual_hierarchy.s5_prefix_anchor import (
            verified_completed_prefix,
        )

        try:
            anchored = verified_completed_prefix(output_root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # The anchor never authorizes execution. A changed or unavailable
            # anchor restores the original complete semantic-audit path.
            print(f"S5 prefix anchor unavailable; using full audit: {exc}")
        else:
            if not anchored <= completed:
                raise ValueError("anchored S5 prefix is not completed")
    for shard_id in ANNUAL_SHARD_IDS:
        if shard_id in completed:
            if shard_id in anchored:
                continue
            worker, _summary = _audited_completed_worker(
                _shard_directory(shard_id, output_root), shard_id, context
            )
            _require_completed_worker_binding(
                output_root, shard_id, worker, context, authority
            )


def _require_completed_worker_binding(
    output_root: Path,
    shard_id: str,
    worker: Mapping[str, object],
    context: Mapping[str, object],
    authority: Mapping[str, object],
) -> None:
    """Require durable process and artifact evidence before skipping a shard."""
    wave_index = wave_index_for_request((shard_id,))
    transition = load_transition(output_root)
    for path in _supervision_paths(output_root, wave_index):
        record = _mapping(json.loads(path.read_text()), "retained S5 supervision")
        if record.get("schema_version") == 2:
            from experiments.case118_annual_hierarchy.s5_speculative_runtime import (
                validate_wave,
            )

            validated = validate_wave(record, output_root)
            if not historical_provenance_matches(
                record, transition, context, authority, output_root=output_root
            ):
                raise ValueError("speculative completed shard provenance mismatch")
            if validated["worker_results"].get(shard_id) == worker:
                return
            continue
        if (
            record.get("schema_version") != SCHEMA_VERSION
            or record.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or record.get("wave_index") != wave_index
            or record.get("frozen_wave") != list(ANNUAL_WAVES[wave_index])
            or not historical_provenance_matches(
                record, transition, context, authority, output_root=output_root
            )
            or record.get("classification")
            not in {
                "accepted",
                "resource_limit",
                "supervisor_interrupted",
                "supervisor_failure",
                "worker_launch_failure",
                "artifact_failure",
                "worker_process_failure",
            }
        ):
            raise ValueError("S5 retained supervision identity or provenance mismatch")
        requested = record.get("requested_shards")
        if (
            not isinstance(requested, list)
            or wave_index_for_request(requested) != wave_index
        ):
            raise ValueError("S5 retained supervision has an invalid shard request")
        results = _mapping(record.get("worker_results"), "retained S5 workers")
        codes = _mapping(record.get("returncodes"), "retained S5 return codes")
        code = codes.get(shard_id)
        if (
            shard_id not in requested
            or shard_id not in results
            or isinstance(code, bool)
            or not isinstance(code, int)
            or code != 0
        ):
            continue
        if results[shard_id] != worker:
            raise ValueError(
                "S5 retained supervision embeds a different completed worker"
            )
        roots = _mapping(record.get("worker_root_pids"), "retained S5 worker roots")
        pid = roots.get(shard_id)
        logs = _mapping(record.get("worker_logs"), "retained S5 worker logs")
        log = _mapping(logs.get(shard_id), "retained S5 completed worker log")
        relative = log.get("path")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or not isinstance(relative, str)
            or Path(relative).name != relative
            or sha256_path(output_root / relative) != log.get("sha256")
        ):
            raise ValueError("S5 completed worker process/log evidence is invalid")
        return
    raise ValueError(
        f"S5 completed shard {shard_id} has no bound zero-exit supervision outcome. "
        "Its result and archives are preserved. Restore the matching retained "
        "supervision evidence, or obtain a separately reviewed audit-only "
        "reconciliation before continuing; do not delete or rerun the shard."
    )


def supervise_wave(
    shard_ids: Sequence[str],
    *,
    authority_path: Path = DEFAULT_NUMERICAL_AUTHORITY_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    reviewed_resume: bool = False,
    use_completed_prefix_anchor: bool = False,
    poll_seconds: float = POLL_SECONDS,
    observation_reader: Callable[
        [], Sequence[ProcessObservation]
    ] = process_observations,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    terminate_process: Callable[[subprocess.Popen[bytes], float], None] = _terminate,
) -> Mapping[str, object]:
    """Run one reviewed subset of a frozen two-shard wave."""
    wave_index = wave_index_for_request(shard_ids)
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("S5 supervision requires a clean committed worktree")
    authority = load_numerical_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    require_current_transition(output_root, context, authority)
    if authority.get("recovery_policy") is not None:
        from experiments.case118_annual_hierarchy.s5_speculative_runtime import run_wave

        return run_wave(
            shard_ids,
            authority_path=authority_path,
            output_root=output_root,
            reviewed_resume=reviewed_resume,
            use_completed_prefix_anchor=use_completed_prefix_anchor,
            poll_seconds=poll_seconds,
        )
    _outer()
    directories = [_shard_directory(item, output_root) for item in shard_ids]
    if any(path.exists() for path in directories) and not reviewed_resume:
        raise FileExistsError("S5 shard output already exists")
    output_root.mkdir(parents=True, exist_ok=True)
    started = clock()
    processes: dict[str, subprocess.Popen[bytes]] = {}
    logs: dict[str, tuple[Path, Any]] = {}
    samples: list[Mapping[str, object]] = []
    triggers: list[Mapping[str, object]] = []
    peak_per_worker = {shard_id: 0.0 for shard_id in shard_ids}
    peak_aggregate = 0.0
    maximum_concurrency = 0
    interruption: BaseException | None = None
    supervisor_error: Exception | None = None
    launch_error: Exception | None = None
    try:
        for shard_id, directory in zip(shard_ids, directories, strict=True):
            log_path = output_root / (
                f"wave-{wave_index:03d}-{shard_id}-"
                f"{len(_supervision_paths(output_root, wave_index)):03d}.log"
            )
            log = log_path.open("xb")
            logs[shard_id] = (log_path, log)
            command = [
                sys.executable,
                "-m",
                "experiments.case118_annual_hierarchy.run_s4b",
                "--worker",
                "--execution-scope",
                ANNUAL_SCOPE,
                "--execution-mode",
                "annual",
                "--directory",
                str(directory),
                "--shard-id",
                shard_id,
                "--authority",
                str(authority_path),
            ]
            if reviewed_resume:
                command.append("--reviewed-resume")
            try:
                process = popen(
                    command,
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except Exception as exc:
                launch_error = exc
                break
            processes[shard_id] = process
        while launch_error is None and any(
            process.poll() is None for process in processes.values()
        ):
            active = {
                shard_id: process
                for shard_id, process in processes.items()
                if process.poll() is None
            }
            maximum_concurrency = max(maximum_concurrency, len(active))
            if active:
                observations = observation_reader()
                usage = process_tree_usage(
                    [process.pid for process in active.values()], observations
                )
                own = next(
                    (item for item in observations if item.pid == os.getpid()), None
                )
                if own is None:
                    raise ValueError("S5 supervisor current-RSS sample is unavailable")
                per_worker = cast(
                    Mapping[str, Mapping[str, object]], usage["per_worker"]
                )
                for shard_id, process in active.items():
                    rss = float(cast(float, per_worker[str(process.pid)]["rss_mib"]))
                    peak_per_worker[shard_id] = max(peak_per_worker[shard_id], rss)
                    if rss > PER_WORKER_RSS_LIMIT_MIB:
                        triggers.append(
                            {
                                "kind": "per_worker_rss_limit",
                                "shard_id": shard_id,
                                "rss_mib": rss,
                            }
                        )
                aggregate = float(cast(float, usage["aggregate_rss_mib"]))
                peak_aggregate = max(peak_aggregate, aggregate)
                if aggregate > AGGREGATE_RSS_LIMIT_MIB:
                    triggers.append(
                        {"kind": "aggregate_rss_limit", "rss_mib": aggregate}
                    )
                samples.append(
                    {
                        "elapsed_seconds": clock() - started,
                        "active_shards": sorted(active),
                        "supervisor_current_rss_mib": own.rss_mib,
                        "supervisor_cpu_seconds": own.cpu_seconds,
                        **usage,
                    }
                )
            if triggers:
                for process in active.values():
                    terminate_process(process, WORKER_TERMINATION_GRACE_SECONDS)
                break
            sleep(poll_seconds)
        if launch_error is not None:
            for process in processes.values():
                terminate_process(process, WORKER_TERMINATION_GRACE_SECONDS)
    except Exception as exc:
        for process in processes.values():
            terminate_process(process, WORKER_TERMINATION_GRACE_SECONDS)
        supervisor_error = exc
    except BaseException as exc:
        for process in processes.values():
            terminate_process(process, WORKER_TERMINATION_GRACE_SECONDS)
        interruption = exc
    finally:
        for _path, handle in logs.values():
            handle.close()
    returncodes = {name: process.wait() for name, process in processes.items()}
    results: dict[str, Mapping[str, object]] = {}
    artifact_error: str | None = None
    if (
        interruption is None
        and supervisor_error is None
        and launch_error is None
        and not triggers
        and not samples
    ):
        artifact_error = "S5 supervision retained no process-tree sample"
    # A completed peer remains usable even when the wave must stop for review.
    for shard_id, directory in zip(shard_ids, directories, strict=True):
        if returncodes.get(shard_id) != 0:
            continue
        try:
            worker, _summary = _audited_completed_worker(directory, shard_id, context)
            results[shard_id] = worker
        except Exception as exc:
            artifact_error = f"{shard_id}: {type(exc).__name__}: {exc}"
    classification = (
        "resource_limit"
        if triggers
        else "supervisor_interrupted"
        if interruption is not None
        else "supervisor_failure"
        if supervisor_error is not None
        else "worker_launch_failure"
        if launch_error is not None
        else "artifact_failure"
        if artifact_error is not None
        else "worker_process_failure"
        if set(returncodes) != set(shard_ids)
        or any(value != 0 for value in returncodes.values())
        else "accepted"
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "wave_index": wave_index,
        "frozen_wave": list(ANNUAL_WAVES[wave_index]),
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "authority": authority,
        "execution_context": context,
        "requested_shards": list(shard_ids),
        "requested_concurrency": len(shard_ids),
        "maximum_observed_concurrency": maximum_concurrency,
        "returncodes": returncodes,
        "worker_root_pids": {
            shard_id: process.pid for shard_id, process in processes.items()
        },
        "resource_triggers": triggers,
        "resource_samples": samples,
        "peak_worker_rss_mib": peak_per_worker,
        "peak_aggregate_rss_mib": peak_aggregate,
        "elapsed_critical_path_seconds": clock() - started,
        "artifact_error": artifact_error,
        "supervisor_exception": (
            f"{type(interruption).__name__}: {interruption}"
            if interruption is not None
            else f"{type(supervisor_error).__name__}: {supervisor_error}"
            if supervisor_error is not None
            else f"{type(launch_error).__name__}: {launch_error}"
            if launch_error is not None
            else None
        ),
        "supervisor_exception_kind": (
            "interruption"
            if interruption is not None
            else "failure"
            if supervisor_error is not None
            else "launch_failure"
            if launch_error is not None
            else None
        ),
        "worker_results": results,
        "worker_logs": {
            shard_id: {"path": path.name, "sha256": sha256_path(path)}
            for shard_id, (path, _handle) in logs.items()
        },
    }
    record_path = _next_supervision_path(output_root, wave_index)
    atomic_immutable_json(record_path, payload)
    if interruption is not None:
        raise interruption
    return {
        **payload,
        "record_path": record_path.name,
        "record_sha256": sha256_path(record_path),
    }


def _progress_payload(
    *,
    context: Mapping[str, object],
    authority: Mapping[str, object],
    classification: str,
    next_wave: int,
    completed_shards: Sequence[str],
    supervision_records: Sequence[Mapping[str, object]],
    reviewed_continuations: Sequence[Mapping[str, object]],
    root_outcomes: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "classification": classification,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "execution_context": context,
        "authority": authority,
        "next_wave": next_wave,
        "completed_shards": list(completed_shards),
        "supervision_records": list(supervision_records),
        "reviewed_continuations": list(reviewed_continuations),
        "root_outcomes": list(root_outcomes),
    }


def _completed_shards(output_root: Path) -> list[str]:
    """Return completed shard identities in canonical manifest order."""
    return [
        shard_id
        for shard_id in ANNUAL_SHARD_IDS
        if (_shard_directory(shard_id, output_root) / "shard-result.json").is_file()
    ]


def _supervision_reference(
    result: Mapping[str, object], output_root: Path, wave_index: int
) -> Mapping[str, object]:
    relative = result.get("record_path")
    if not isinstance(relative, str) or Path(relative).name != relative:
        raise ValueError("S5 supervisor returned an invalid record path")
    path = output_root / relative
    retained = _mapping(json.loads(path.read_text()), "S5 retained supervision")
    digest = sha256_path(path)
    if (
        result.get("record_sha256") != digest
        or retained.get("wave_index") != wave_index
        or retained.get("classification") != result.get("classification")
    ):
        raise ValueError("S5 supervisor return differs from retained record")
    return {
        "path": relative,
        "sha256": digest,
        "wave_index": wave_index,
        "classification": result["classification"],
    }


def _reconcile_supervision_records(
    output_root: Path, records: list[Mapping[str, object]]
) -> None:
    """Attach a supervision record published before a catchable root failure."""
    actual: list[Mapping[str, object]] = []
    for path in sorted(output_root.glob("supervision-wave-*.json")):
        value = _mapping(json.loads(path.read_text()), "S5 supervision")
        item = {
            "path": path.name,
            "sha256": sha256_path(path),
            "wave_index": value["wave_index"],
            "classification": value["classification"],
        }
        if value.get("schema_version") == 2:
            from experiments.case118_annual_hierarchy.s5_speculative_runtime import (
                validate_wave,
            )

            # Helpers are contenders, not shard workers. Validate their actual
            # launch/cleanup receipts, including a failed pre-solve launch.
            validate_wave(value, output_root)
        elif value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("retained S5 supervision schema is invalid")
        if (
            value.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or value.get("execution_context") is None
            or value.get("authority") is None
        ):
            raise ValueError("retained S5 supervision identity is invalid")
        actual.append(item)
    if actual[: len(records)] != records:
        raise ValueError("retained S5 supervision registry is discontinuous")
    records.extend(actual[len(records) :])


def _publish_or_verify(path: Path, value: Mapping[str, object]) -> None:
    """Publish immutable root evidence, or accept an identical prior write."""
    if path.is_file():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"existing S5 root artifact differs: {path.name}")
        return
    atomic_immutable_json(path, value)


def _record_root_outcome(
    output_root: Path,
    payload: Mapping[str, object],
    outcomes: list[Mapping[str, object]],
) -> None:
    path = output_root / f"root-outcome-{len(outcomes):03d}.json"
    _publish_or_verify(path, payload)
    outcomes.append(
        {
            "path": path.name,
            "sha256": sha256_path(path),
            "classification": payload["classification"],
            "next_wave": payload["next_wave"],
        }
    )


def _reconcile_root_outcomes(
    output_root: Path, outcomes: list[Mapping[str, object]]
) -> None:
    """Attach an immutable root outcome published before progress replacement."""
    actual: list[Mapping[str, object]] = []
    prior: list[Mapping[str, object]] = []
    for path in sorted(output_root.glob("root-outcome-*.json")):
        value = _mapping(json.loads(path.read_text()), "S5 root outcome")
        item = {
            "path": path.name,
            "sha256": sha256_path(path),
            "classification": value["classification"],
            "next_wave": value["next_wave"],
        }
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or value.get("root_outcomes") != prior
        ):
            raise ValueError("retained S5 root-outcome chain is invalid")
        actual.append(item)
        prior.append(item)
    if actual[: len(outcomes)] != outcomes:
        raise ValueError("retained S5 root-outcome registry is discontinuous")
    outcomes.extend(actual[len(outcomes) :])


def run_annual(
    *,
    authority_path: Path = DEFAULT_NUMERICAL_AUTHORITY_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    reviewed_continue: bool = False,
    use_completed_prefix_anchor: bool = False,
    source_transition_path: Path | None = None,
    operator_intervention_path: Path | None = None,
    diagnostic_root: Path | None = None,
    supervisor: Callable[..., Mapping[str, object]] = supervise_wave,
) -> Mapping[str, object]:
    """Execute the six frozen waves, stopping on the first abnormal outcome."""
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("S5 annual execution requires a clean committed worktree")
    authority = load_numerical_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    transition: dict[str, Any] | None
    if source_transition_path is not None and operator_intervention_path is not None:
        raise ValueError("S5 accepts only one new reviewed transition per invocation")
    if source_transition_path is not None:
        if not reviewed_continue or not output_root.is_dir():
            raise ValueError(
                "S5 source transition requires explicit reviewed continuation"
            )
        transition = publish_transition(
            output_root, source_transition_path, context, authority
        )
    elif operator_intervention_path is not None:
        if not reviewed_continue or not output_root.is_dir() or diagnostic_root is None:
            raise ValueError(
                "S5 operator intervention requires reviewed continuation and diagnostic evidence"
            )
        from experiments.case118_annual_hierarchy.s5_operator_intervention import (
            publish_intervention,
        )
        from experiments.case118_annual_hierarchy.s5_source_transition import (
            load_base_transition,
        )

        predecessor = load_base_transition(output_root)
        if predecessor is None:
            raise ValueError("S5 operator intervention lacks predecessor transition")
        publish_intervention(
            output_root,
            operator_intervention_path,
            diagnostic_root,
            context,
            authority,
            predecessor_transition=predecessor,
        )
        transition = load_transition(output_root)
    else:
        transition = load_transition(output_root)
    if transition is not None:
        if (
            transition["new_authority"] != authority
            or transition["contract"]["continuation_execution"]["context"] != context
        ):
            raise ValueError("S5 root does not match the reviewed source transition")
    elif authority.get("source_version_contract_sha256") is not None:
        raise ValueError("S5 source-version authority lacks its transition record")
    _outer()
    progress_path = output_root / "progress.json"
    if not output_root.exists():
        if reviewed_continue:
            raise FileNotFoundError("reviewed S5 continuation requires prior output")
        output_root.mkdir(parents=True, exist_ok=False)
        supervision_records: list[Mapping[str, object]] = []
        continuations: list[Mapping[str, object]] = []
        root_outcomes: list[Mapping[str, object]] = []
        next_wave = 0
        atomic_immutable_json(output_root / "run-context.json", context)
    else:
        if not reviewed_continue:
            raise FileExistsError("S5 output requires explicit reviewed continuation")
        completed_result_path = output_root / "run-result.json"
        if completed_result_path.is_file():
            completed_result = _mapping(
                json.loads(completed_result_path.read_text()), "S5 run result"
            )
            if (
                completed_result.get("classification") != "accepted"
                or completed_result.get("execution_context") != context
                or completed_result.get("authority") != authority
            ):
                raise ValueError("existing S5 result does not match reviewed execution")
            atomic_json(progress_path, completed_result)
            return completed_result
        if not progress_path.is_file():
            retained_context_path = output_root / "run-context.json"
            if (
                not retained_context_path.is_file()
                or json.loads(retained_context_path.read_text()) != context
            ):
                raise ValueError("S5 zero-boundary recovery lacks its frozen context")
            supervision_records = []
            continuations = []
            root_outcomes = []
            next_wave = 0
            atomic_json(
                progress_path,
                _progress_payload(
                    context=context,
                    authority=authority,
                    classification="supervisor_interrupted",
                    next_wave=0,
                    completed_shards=_completed_shards(output_root),
                    supervision_records=(),
                    reviewed_continuations=(),
                    root_outcomes=(),
                ),
            )
        progress = _mapping(json.loads(progress_path.read_text()), "S5 progress")
        if not historical_provenance_matches(
            progress, transition, context, authority, output_root=output_root
        ) or progress.get("classification") not in {
            "partial",
            "supervisor_interrupted",
            "driver_failure",
            "running",
        }:
            raise ValueError("S5 reviewed continuation provenance or state mismatch")
        _validate_completed_prefix(
            output_root,
            progress["next_wave"],
            context,
            authority,
            use_completed_prefix_anchor=use_completed_prefix_anchor,
        )
        next_wave = cast(int, progress["next_wave"])
        supervision_records = list(
            cast(Sequence[Mapping[str, object]], progress["supervision_records"])
        )
        continuations = list(
            cast(Sequence[Mapping[str, object]], progress["reviewed_continuations"])
        )
        root_outcomes = list(
            cast(Sequence[Mapping[str, object]], progress.get("root_outcomes", ()))
        )
        _reconcile_supervision_records(output_root, supervision_records)
        _reconcile_root_outcomes(output_root, root_outcomes)
        reviewed_source = _progress_payload(
            context=_mapping(progress["execution_context"], "prior context"),
            authority=_mapping(progress["authority"], "prior authority"),
            classification=str(progress["classification"]),
            next_wave=next_wave,
            completed_shards=_completed_shards(output_root),
            supervision_records=supervision_records,
            reviewed_continuations=continuations,
            root_outcomes=root_outcomes,
        )
        continuation = {
            "schema_version": SCHEMA_VERSION,
            "classification": "explicit_reviewed_continuation",
            "next_wave": next_wave,
            "reviewed_source": reviewed_source,
            "reviewed_source_sha256": object_sha256(reviewed_source),
            "prior_supervision_sha256": (
                supervision_records[-1]["sha256"] if supervision_records else None
            ),
            "prior_root_outcome_sha256": (
                root_outcomes[-1]["sha256"] if root_outcomes else None
            ),
            "execution_context": context,
            "authority": authority,
        }
        continuation_path = (
            output_root / f"reviewed-continuation-{len(continuations):03d}.json"
        )
        atomic_immutable_json(continuation_path, continuation)
        continuations.append(
            {"path": continuation_path.name, "sha256": sha256_path(continuation_path)}
        )
    atomic_json(
        progress_path,
        _progress_payload(
            context=context,
            authority=authority,
            classification="running",
            next_wave=next_wave,
            completed_shards=_completed_shards(output_root),
            supervision_records=supervision_records,
            reviewed_continuations=continuations,
            root_outcomes=root_outcomes,
        ),
    )
    interruption: BaseException | None = None
    try:
        while next_wave < len(ANNUAL_WAVES):
            wave = ANNUAL_WAVES[next_wave]
            pending = tuple(
                shard_id
                for shard_id in wave
                if not (
                    _shard_directory(shard_id, output_root) / "shard-result.json"
                ).is_file()
            )
            if pending:
                supervisor_kwargs: dict[str, object] = {
                    "authority_path": authority_path,
                    "output_root": output_root,
                    "reviewed_resume": reviewed_continue,
                }
                if use_completed_prefix_anchor:
                    supervisor_kwargs["use_completed_prefix_anchor"] = True
                result = supervisor(pending, **supervisor_kwargs)
                supervision_records.append(
                    _supervision_reference(result, output_root, next_wave)
                )
                if result["classification"] != "accepted":
                    outcome = _progress_payload(
                        context=context,
                        authority=authority,
                        classification="partial",
                        next_wave=next_wave,
                        completed_shards=_completed_shards(output_root),
                        supervision_records=supervision_records,
                        reviewed_continuations=continuations,
                        root_outcomes=root_outcomes,
                    )
                    _record_root_outcome(output_root, outcome, root_outcomes)
                    payload = _progress_payload(
                        context=context,
                        authority=authority,
                        classification="partial",
                        next_wave=next_wave,
                        completed_shards=_completed_shards(output_root),
                        supervision_records=supervision_records,
                        reviewed_continuations=continuations,
                        root_outcomes=root_outcomes,
                    )
                    atomic_json(progress_path, payload)
                    return payload
            for shard_id in wave:
                _audited_completed_worker(
                    _shard_directory(shard_id, output_root),
                    shard_id,
                    context,
                )
            next_wave += 1
            atomic_json(
                progress_path,
                _progress_payload(
                    context=context,
                    authority=authority,
                    classification="running",
                    next_wave=next_wave,
                    completed_shards=_completed_shards(output_root),
                    supervision_records=supervision_records,
                    reviewed_continuations=continuations,
                    root_outcomes=root_outcomes,
                ),
            )
    except BaseException as exc:
        interruption = exc
        _reconcile_supervision_records(output_root, supervision_records)
        _reconcile_root_outcomes(output_root, root_outcomes)
        classification = (
            "supervisor_interrupted"
            if isinstance(exc, (KeyboardInterrupt, _SupervisorInterrupted))
            else "driver_failure"
        )
        outcome = _progress_payload(
            context=context,
            authority=authority,
            classification=classification,
            next_wave=next_wave,
            completed_shards=_completed_shards(output_root),
            supervision_records=supervision_records,
            reviewed_continuations=continuations,
            root_outcomes=root_outcomes,
        )
        _record_root_outcome(output_root, outcome, root_outcomes)
        atomic_json(
            progress_path,
            _progress_payload(
                context=context,
                authority=authority,
                classification=classification,
                next_wave=next_wave,
                completed_shards=_completed_shards(output_root),
                supervision_records=supervision_records,
                reviewed_continuations=continuations,
                root_outcomes=root_outcomes,
            ),
        )
    if interruption is not None:
        raise interruption
    summaries = [
        _audited_completed_worker(
            _shard_directory(shard_id, output_root), shard_id, context
        )[1]
        for shard_id in ANNUAL_SHARD_IDS
    ]
    merged = merge_shard_summaries(
        summaries,
        registry_shards=[shard_entry(item)[1] for item in ANNUAL_SHARD_IDS],
        expected_execution_registry_sha256=_annual_registry_sha256(),
        allowed_execution_source_fingerprints=(
            tuple(
                str(pair_context["source_fingerprint"])
                for pair_context, _ in transition_context_authority_pairs(transition)
            )
            if transition is not None
            else None
        ),
    )
    merged_path = output_root / "merged-result.json"
    _publish_or_verify(merged_path, merged)
    payload = {
        **_progress_payload(
            context=context,
            authority=authority,
            classification="accepted",
            next_wave=len(ANNUAL_WAVES),
            completed_shards=list(ANNUAL_SHARD_IDS),
            supervision_records=supervision_records,
            reviewed_continuations=continuations,
            root_outcomes=root_outcomes,
        ),
        "merged_result": {"path": merged_path.name, "sha256": sha256_path(merged_path)},
    }
    result_path = output_root / "run-result.json"
    _publish_or_verify(result_path, payload)
    atomic_json(progress_path, payload)
    return payload


def run_annual_supervised(
    *,
    authority_path: Path = DEFAULT_NUMERICAL_AUTHORITY_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    reviewed_continue: bool = False,
    use_completed_prefix_anchor: bool = False,
    source_transition_path: Path | None = None,
    operator_intervention_path: Path | None = None,
    diagnostic_root: Path | None = None,
) -> Mapping[str, object]:
    """Install a catchable SIGTERM boundary around the annual root driver."""
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupt(_signum: int, _frame: object) -> None:
        raise _SupervisorInterrupted("S5 root supervisor received SIGTERM")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        return run_annual(
            authority_path=authority_path,
            output_root=output_root,
            reviewed_continue=reviewed_continue,
            use_completed_prefix_anchor=use_completed_prefix_anchor,
            source_transition_path=source_transition_path,
            operator_intervention_path=operator_intervention_path,
            diagnostic_root=diagnostic_root,
        )
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authority", type=Path, default=DEFAULT_NUMERICAL_AUTHORITY_PATH
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--reviewed-continue", action="store_true")
    parser.add_argument("--use-completed-prefix-anchor", action="store_true")
    parser.add_argument("--source-transition", type=Path)
    parser.add_argument("--operator-intervention", type=Path)
    parser.add_argument("--diagnostic-root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_annual_supervised(
                authority_path=args.authority,
                output_root=args.output_root.resolve(),
                reviewed_continue=args.reviewed_continue,
                use_completed_prefix_anchor=args.use_completed_prefix_anchor,
                source_transition_path=args.source_transition,
                operator_intervention_path=args.operator_intervention,
                diagnostic_root=args.diagnostic_root,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
