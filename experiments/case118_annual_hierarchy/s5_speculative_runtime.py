"""Opt-in S5 wave orchestration using fresh single-attempt subprocesses.

The ordinary root schedule and annual merger are unchanged. This adapter owns
only a reviewed wave's new-policy windows, and writes ordinary shard summaries
plus a versioned wave receipt. It cannot create execution authority.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import gzip
import json
from pathlib import Path
import signal
import sys
import time
from typing import Any, Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.s4b_manifest import S4_OUTER_ARCHIVE_PATH
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_execution import (
    audit_shard,
    shard_entry,
    shard_checkpoint_payload,
    verify_shard_artifacts,
)
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    object_sha256,
)
from experiments.case118_annual_hierarchy.s5_execution import (
    ANNUAL_WAVES,
    annual_registry,
    execution_context,
    load_numerical_authority,
    wave_index_for_request,
)
from experiments.case118_annual_hierarchy.s5_source_transition import (
    require_current_transition,
)
from experiments.case118_annual_hierarchy.s5_speculative_archive import (
    audit_candidate,
    invocation,
)
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    SelectedController,
)
from experiments.case118_annual_hierarchy.streaming_runner import CausalControllerSource
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    POLICY_NAME,
    WindowKey,
)
from experiments.case118_annual_hierarchy.s5_speculative_process import (
    SubprocessBackend,
    artifact_ref,
)
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
    SpeculativeSupervisor,
    ResourceStop,
)
from experiments.case118_annual_hierarchy.s5_speculative_transaction import (
    pending_winner,
)
from experiments.case118_annual_hierarchy.s5_speculative_window import (
    finalize_window,
    publish_window,
    validate_window,
)
from experiments.case118_annual_hierarchy.s5_speculative_worker import source_payload
from experiments.case118_annual_hierarchy.streaming_archive import (
    causal_source_from_archive,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)


class WaveInterrupted(BaseException):
    pass


class WaveAdapter:
    def __init__(
        self,
        output_root: Path,
        authority_path: Path,
        context: Mapping[str, object],
        contract_sha256: str,
    ) -> None:
        self.output_root, self.authority_path, self.context = (
            output_root,
            authority_path,
            context,
        )
        self.contract_sha256 = contract_sha256
        self.fixture, self.outer = load_s4_fixture(), _outer()
        self.entries: dict[WindowKey, WindowIndexEntry] = {}
        self.directories: dict[str, Path] = {}

    def shard_directory(self, key: WindowKey) -> Path:
        return self.output_root / f"shard-{int(key.shard_id[-3:]):03d}"

    def command(self, spec: AttemptSpec, directory: Path) -> Sequence[str]:
        shard_dir = self.shard_directory(spec.window)
        cp_path = shard_dir / "checkpoint.json"
        checkpoint = json.loads(cp_path.read_text())
        if checkpoint["next_global_iteration"] != spec.window.iteration:
            raise ValueError("new contender is not at the physical checkpoint")
        source: CausalControllerSource | SelectedController | None = None
        if checkpoint["windows"]:
            ref = checkpoint["windows"][-1]
            path = shard_dir / ref["relative_path"]
            if (
                path.stat().st_size != ref["bytes"]
                or sha256_path(path) != ref["sha256"]
            ):
                raise ValueError("preceding window hash mismatch")
            with gzip.open(path, "rt") as stream:
                previous = json.load(stream)
            selected = previous["executed_interval"]["controlling_attempt_id"]
            if previous["schema_version"] == 2:
                prior_dir = (
                    self.output_root / previous["candidate_directories"][selected]
                )
                prior_spec = invocation(
                    json.loads((prior_dir / "result.json").read_text())["invocation"]
                )
                prior = audit_candidate(
                    prior_dir,
                    prior_spec,
                    inputs=self.fixture.inputs,
                    policy=self.fixture.policy,
                    outer=self.outer,
                    initial=dict(
                        zip(
                            previous["storage_device_ids"],
                            previous["initial_soc_mwh"],
                            strict=True,
                        )
                    ),
                    stop=previous["interval_stop"],
                )
                source = prior.selected_source()
            else:
                source = causal_source_from_archive(
                    next(
                        item
                        for item in previous["attempts"]
                        if item["attempt_id"] == selected
                    )
                )
        free_directory = None
        if spec.source_slot in (2, 3, 4, 5) and spec.replay_of is None:
            candidates = []
            for path in self.directories.values():
                result_path = path / "result.json"
                if not result_path.is_file():
                    continue
                payload = json.loads(result_path.read_text())
                candidate = invocation(payload["invocation"])
                audit = cast(Mapping[str, Any], payload["attempt"]).get("audit")
                if (
                    candidate.window == spec.window
                    and candidate.source_slot == 1
                    and isinstance(audit, Mapping)
                    and audit.get("accepted_primal") is True
                ):
                    candidates.append((candidate.order, path))
            if candidates:
                free_directory = max(candidates)[1]
        replay_directory = self.directories.get(spec.replay_of or "")
        serialized = source_payload(source)
        request = {
            "invocation": asdict(spec),
            "execution_context": dict(self.context),
            "contract_sha256": self.contract_sha256,
            "authority_path": str(self.authority_path.resolve()),
            "output_root": str(self.output_root.resolve()),
            "checkpoint_path": str(cp_path.resolve()),
            "checkpoint_sha256": sha256_path(cp_path),
            "preceding_source": serialized,
            "source_sha256": object_sha256(serialized),
            "target_free_directory": None
            if free_directory is None
            else str(free_directory.resolve()),
            "replay_start": None
            if replay_directory is None
            else str((replay_directory / "start.json").resolve()),
        }
        atomic_immutable_json(directory / "request.json", request)
        self.directories[spec.attempt_id] = directory
        return [
            sys.executable,
            "-m",
            "experiments.case118_annual_hierarchy.s5_speculative_worker",
            "--directory",
            str(directory),
        ]

    def audit(self, spec: AttemptSpec, directory: Path) -> Completion:
        checkpoint = json.loads(
            (self.shard_directory(spec.window) / "checkpoint.json").read_text()
        )
        return audit_candidate(
            directory,
            spec,
            inputs=self.fixture.inputs,
            policy=self.fixture.policy,
            outer=self.outer,
            initial=dict(
                zip(
                    checkpoint["storage_device_ids"],
                    checkpoint["realized_soc_mwh"],
                    strict=True,
                )
            ),
            stop=min(
                spec.window.iteration + self.fixture.policy.ac_window_steps,
                checkpoint["interval"]["stop"],
            ),
        ).completion

    def publish(
        self,
        winner: AttemptSpec,
        completions: Sequence[Completion],
        directories: Mapping[str, Path],
    ) -> None:
        self.entries[winner.window] = publish_window(
            self.shard_directory(winner.window),
            winner,
            completions,
            directories,
            inputs=self.fixture.inputs,
            policy=self.fixture.policy,
            outer=self.outer,
            context=self.context,
            contract_sha256=self.contract_sha256,
        )

    def advance(self, winner: AttemptSpec, directories: Mapping[str, Path]) -> None:
        self.reconcile(self.shard_directory(winner.window), self.entries[winner.window])

    def reconcile(self, directory: Path, entry: WindowIndexEntry) -> None:
        checkpoint = json.loads((directory / "checkpoint.json").read_text())

        def validate(window: Mapping[str, Any]) -> None:
            validate_window(
                window,
                directory,
                inputs=self.fixture.inputs,
                policy=self.fixture.policy,
                outer=self.outer,
                trajectory_stop=checkpoint["interval"]["stop"],
            )

        finalize_window(directory, entry, before_advance=validate)


def _validate_helper_lifecycle(
    events: Sequence[Mapping[str, Any]],
    lifecycles: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[int, set[str]]:
    """Reconstruct the one-helper lease and its causal assistance eligibility."""
    active: dict[str, AttemptSpec] = {}
    seen: set[str] = set()
    primaries: dict[WindowKey, AttemptSpec] = {}
    returned: set[str] = set()
    waiting: dict[WindowKey, float] = {}
    shared_helpers: set[str] = set()
    replacement_helpers: set[str] = set()
    peak = 0
    for event in events:
        raw = event["invocation"]
        if raw is None:
            continue
        spec = invocation(raw)
        key, window = spec.attempt_id, spec.window
        stamp = event["monotonic_seconds"]
        if not np.isfinite(stamp) or stamp < 0:
            raise ValueError("invalid lifecycle clock")
        kind = event["kind"]
        if kind == "helper_eligible":
            if spec.order != 0 or window not in primaries or window in waiting:
                raise ValueError("helper eligibility lacks a unique primary request")
            if any(active[item].window == window for item in shared_helpers):
                raise ValueError("active helper cannot request another lease")
            if lifecycles is not None:
                life = lifecycles[key]
                phases = {
                    item["phase"]: item["monotonic_seconds"] for item in life["phases"]
                }
                if key in returned:
                    completion = life.get("completion")
                    if completion is None or completion["outcome"] not in {
                        "rejected",
                        "construction_error",
                    }:
                        raise ValueError("early assistance requires a failed primary")
                elif (
                    "before_ac_solve" not in phases
                    or stamp < phases["before_ac_solve"] + 300.0
                    or phases.get("after_ac_solve", stamp)
                    < phases["before_ac_solve"] + 300.0
                ):
                    raise ValueError("helper eligibility precedes primary solve budget")
            waiting[window] = stamp
        elif kind in {"launched", "replacement_launched"}:
            if key in seen:
                raise ValueError("duplicate contender launch")
            if spec.order == 0:
                if kind != "launched":
                    raise ValueError("a primary cannot use a replacement lane")
                if window in primaries or any(
                    item.window.shard_id == window.shard_id for item in active.values()
                ):
                    raise ValueError("overlapping primary windows on one shard")
                primaries[window] = spec
            elif kind == "replacement_launched":
                primary = primaries.get(window)
                if (
                    spec.order < 11
                    or primary is None
                    or primary.attempt_id not in returned
                    or any(
                        active[item].window == window for item in replacement_helpers
                    )
                ):
                    raise ValueError("replacement launch lacks a freed primary lane")
                replacement_helpers.add(key)
            else:
                if shared_helpers:
                    raise ValueError(
                        "multiple shared helpers are simultaneously active"
                    )
                if window not in waiting or stamp < waiting[window]:
                    raise ValueError("helper launch lacks prior eligibility")
                if window != min(waiting, key=lambda item: (waiting[item], item)):
                    raise ValueError("helper launch violates waiting order")
                waiting.pop(window)
                shared_helpers.add(key)
            seen.add(key)
            active[key] = spec
            peak = max(peak, len(active))
            if len(active) > 3 or sum(item.order == 0 for item in active.values()) > 2:
                raise ValueError("speculative process ceiling exceeded")
        elif kind in {"audited_return", "solve_budget", "lost_race", "memory_pressure"}:
            if key not in active:
                raise ValueError("contender completion lacks an active launch")
            active.pop(key)
            shared_helpers.discard(key)
            replacement_helpers.discard(key)
            if kind == "audited_return":
                returned.add(key)
        elif kind in {"checkpoint_advanced", "helpers_exhausted"}:
            waiting.pop(window, None)
    return peak, set(active)


def _wave_record_concurrency(events: Sequence[Mapping[str, Any]]) -> int:
    """Return the validated process peak published in a wave record."""
    peak, _ = _validate_helper_lifecycle(events)
    return peak


def validate_wave(
    record: Mapping[str, Any], output_root: Path | None = None
) -> Mapping[str, Any]:
    """Validate the new process topology without pretending helpers are shards."""
    requested = record["requested_shards"]
    index = wave_index_for_request(requested)
    if (
        record.get("schema_version") != 2
        or record.get("policy") != POLICY_NAME
        or record.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or record.get("wave_index") != index
        or record.get("frozen_wave") != list(ANNUAL_WAVES[index])
        or record.get("requested_concurrency") != len(requested)
        or record.get("maximum_observed_concurrency", 4) > 3
    ):
        raise ValueError("speculative wave identity/concurrency mismatch")
    if record["classification"] == "accepted" and (
        set(record["worker_results"]) != set(requested)
        or any(record["returncodes"].get(key) != 0 for key in requested)
        or any(
            value["classification"] != "accepted"
            for value in record["worker_results"].values()
        )
    ):
        raise ValueError("accepted speculative wave lacks complete shard audits")
    peak, launches = _validate_helper_lifecycle(record["events"])
    if peak != record["maximum_observed_concurrency"] or (
        record["classification"] == "accepted" and launches
    ):
        raise ValueError("speculative wave process lifecycle does not reconstruct")
    if record["classification"] == "accepted" and (
        record["peak_aggregate_rss_mib"] > 24576
        or any(value > 16384 for value in record["peak_worker_rss_mib"].values())
    ):
        raise ValueError("accepted speculative wave crossed a hard memory limit")
    samples = record["resource_samples"]
    for sample in samples:
        values = [
            sample["aggregate_mib"],
            sample["supervisor_current_rss_mib"],
            *sample["worker_trees_mib"],
        ]
        if any(not np.isfinite(value) or value < 0 for value in values):
            raise ValueError("invalid speculative RSS evidence")
    aggregate_peak = max((item["aggregate_mib"] for item in samples), default=0.0)
    worker_peak = max(
        (max(item["worker_trees_mib"], default=0.0) for item in samples), default=0.0
    )
    if (
        record["peak_worker_rss_mib"] != {"solver_tree": worker_peak}
        or record["peak_aggregate_rss_mib"] != aggregate_peak
    ):
        raise ValueError("speculative RSS peaks do not reconstruct")
    if output_root is not None:
        lifecycles = {}
        for key, ref in record["contender_lifecycles"].items():
            path = (output_root / ref["path"]).resolve()
            if not path.is_relative_to(output_root.resolve()) or ref != {
                **artifact_ref(path),
                "path": str(path.relative_to(output_root.resolve())),
            }:
                raise ValueError("speculative lifecycle artifact mismatch")
            lifecycle = json.loads(path.read_text())
            lifecycles[key] = lifecycle
            if (
                invocation(lifecycle["invocation"]).attempt_id != key
                or lifecycle["reaped"] is not True
                or lifecycle.get("clock_anchor") != record.get("clock_anchor")
            ):
                raise ValueError("wave retains an unreaped or mismatched contender")
        all_launched = {
            invocation(item["invocation"]).attempt_id
            for item in record["events"]
            if item["kind"] in {"launched", "replacement_launched"}
        }
        if not all_launched <= set(record["contender_lifecycles"]):
            raise ValueError("wave omits a launched contender's cleanup")
        _validate_helper_lifecycle(record["events"], lifecycles)
        progress = record["phase_progress"]
        path = output_root / progress["path"]
        if progress != {
            **artifact_ref(path),
            "path": str(path.relative_to(output_root)),
        }:
            raise ValueError("wave supervisor progress identity mismatch")
        retained = json.loads(path.read_text())
        if (
            retained["events"] != record["events"]
            or retained["memory_samples"] != record["resource_samples"]
            or retained.get("clock_anchor") != record.get("clock_anchor")
        ):
            raise ValueError(
                "wave events/resource samples differ from retained progress"
            )
    anchor = record["clock_anchor"]
    if (
        not np.isfinite(anchor["monotonic_seconds"])
        or anchor["monotonic_seconds"] < 0
        or datetime.fromisoformat(anchor["utc"]).utcoffset() is None
    ):
        raise ValueError("wave lacks a paired monotonic/UTC clock anchor")
    triggers = [
        event
        for event in record["events"]
        if event["kind"] in {"hard_rss_crossing", "memory_pressure"}
    ]
    if "resource_triggers" in record and record["resource_triggers"] != triggers:
        raise ValueError("speculative resource triggers do not reconstruct")
    # Normalize the v2 topology into the common annual-report projection.
    return {**record, "resource_triggers": triggers}


def run_wave(
    shard_ids: Sequence[str],
    *,
    authority_path: Path,
    output_root: Path,
    reviewed_resume: bool,
    use_completed_prefix_anchor: bool = False,
    poll_seconds: float = 1.0,
) -> Mapping[str, object]:
    """Numerical entry: unavailable unless the owner has bound the new policy."""
    index = wave_index_for_request(shard_ids)
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("speculative wave requires a clean committed source")
    authority = load_numerical_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    transition = require_current_transition(output_root, context, authority)
    if authority.get("recovery_policy") != POLICY_NAME or transition is None:
        raise ValueError("speculative numerical execution remains unauthorized")
    if poll_seconds <= 0 or poll_seconds > 10:
        raise ValueError("speculative poll interval must be in (0, 10] seconds")
    from experiments.case118_annual_hierarchy.run_s5 import _validate_completed_prefix

    if use_completed_prefix_anchor:
        _validate_completed_prefix(
            output_root,
            index,
            context,
            authority,
            use_completed_prefix_anchor=True,
        )
    else:
        _validate_completed_prefix(output_root, index, context, authority)
    adapter = WaveAdapter(
        output_root, authority_path, context, transition["contract_sha256"]
    )
    registry = str(annual_registry()["registry_sha256"])
    checkpoints: dict[str, dict[str, Any]] = {}
    pending_decisions: dict[str, WindowIndexEntry] = {}
    for shard_id in shard_ids:
        _, shard = shard_entry(shard_id)
        directory = adapter.shard_directory(WindowKey(shard_id, 0))
        if directory.exists():
            if not reviewed_resume:
                raise FileExistsError(
                    "speculative shard requires reviewed continuation"
                )
            checkpoint, _ = verify_shard_artifacts(
                directory,
                shard=shard,
                outer=adapter.outer,
                expected_execution_registry_sha256=registry,
                allowed_execution_modes=("annual",),
            )
            decision = pending_winner(directory)
            if decision is not None:
                pending_decisions[shard_id] = decision
            checkpoint = dict(checkpoint)
        else:
            checkpoint = dict(
                shard_checkpoint_payload(
                    shard=shard,
                    execution_source_fingerprint=str(context["source_fingerprint"]),
                    outer_plan_sha256=sha256_path(S4_OUTER_ARCHIVE_PATH),
                    execution_mode="annual",
                    realized_soc_mwh=cast(Mapping[str, Any], shard["storage"])[
                        "initial_state"
                    ]["soc_mwh"],
                    preceding_controlling_attempt_id=None,
                    windows=(),
                    execution_registry_sha256=registry,
                    allowed_execution_modes=("annual",),
                )
            )
        checkpoints[shard_id] = checkpoint
    # All preflight checks precede output or pointer changes.
    sequence = len(list(output_root.glob(f"supervision-wave-{index:03d}-*.json")))
    phase_root = output_root / f"speculative-wave-{index:03d}-{sequence:03d}"
    phase_root.mkdir(parents=True, exist_ok=False)
    backend = SubprocessBackend(
        phase_root,
        cwd=ROOT,
        command=adapter.command,
        audit=adapter.audit,
        publish=adapter.publish,
        advance=adapter.advance,
    )
    supervisor = SpeculativeSupervisor(backend)
    results: dict[str, Any] = {}
    completed: set[str] = set()
    started = time.monotonic()
    classification = "accepted"
    error = None
    prior_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(signum: int, frame: Any) -> None:
        raise WaveInterrupted("SIGTERM")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        for shard_id, checkpoint in checkpoints.items():
            directory = adapter.shard_directory(WindowKey(shard_id, 0))
            if shard_id in pending_decisions:
                adapter.reconcile(directory, pending_decisions[shard_id])
                checkpoint = json.loads((directory / "checkpoint.json").read_text())
            if checkpoint["complete"]:
                completed.add(shard_id)
                continue
            checkpoint["execution_source_fingerprint"] = context["source_fingerprint"]
            atomic_json(directory / "checkpoint.json", checkpoint)
            supervisor.add_window(
                WindowKey(shard_id, checkpoint["next_global_iteration"]),
                has_preceding=bool(checkpoint["windows"]),
                now=time.monotonic(),
            )
        while len(completed) < len(shard_ids):
            finished = supervisor.tick(time.monotonic())
            for key in finished:
                directory = adapter.shard_directory(key)
                checkpoint = json.loads((directory / "checkpoint.json").read_text())
                if checkpoint["complete"]:
                    completed.add(key.shard_id)
                else:
                    supervisor.add_window(
                        WindowKey(key.shard_id, checkpoint["next_global_iteration"]),
                        has_preceding=True,
                        now=time.monotonic(),
                    )
            if len(completed) < len(shard_ids):
                time.sleep(poll_seconds)
    except BaseException as exc:
        classification = (
            "resource_limit"
            if isinstance(exc, ResourceStop)
            else "supervisor_interrupted"
            if isinstance(exc, (KeyboardInterrupt, WaveInterrupted))
            else "supervisor_failure"
        )
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            supervisor.stop(
                "wave_finished" if classification == "accepted" else classification,
                now=time.monotonic(),
            )
        except BaseException:
            signal.signal(signal.SIGTERM, prior_handler)
            raise
    # Full historical reconstruction may be slow. Do it only after every
    # contender is quiescent, including when a peer failed or was interrupted.
    # A completed peer still receives its audit/summary on a partial wave.
    for shard_id in shard_ids:
        if shard_id not in completed:
            continue
        try:
            directory = adapter.shard_directory(WindowKey(shard_id, 0))
            checkpoint = json.loads((directory / "checkpoint.json").read_text())
            summary = audit_shard(
                directory,
                shard=shard_entry(shard_id)[1],
                outer=adapter.outer,
                expected_execution_registry_sha256=registry,
                allowed_execution_modes=("annual",),
            )
            if summary["classification"] != "accepted":
                raise ValueError("completed speculative shard failed independent audit")
            result = {
                **summary,
                "execution_context": dict(context),
                "execution_mode": "annual",
                "recovery_policy": POLICY_NAME,
            }
            if (
                checkpoint["execution_source_fingerprint"]
                != context["source_fingerprint"]
            ):
                from experiments.case118_annual_hierarchy.s5_source_transition import (
                    completed_shard_finalization_binding,
                )

                result["completed_checkpoint_finalization"] = (
                    completed_shard_finalization_binding(
                        directory, checkpoint, context, transition
                    )
                )
            atomic_immutable_json(directory / "shard-result.json", result)
            results[shard_id] = result
        except BaseException as exc:
            classification = (
                "supervisor_interrupted"
                if isinstance(exc, (KeyboardInterrupt, WaveInterrupted))
                else "supervisor_failure"
            )
            error = f"{error + '; ' if error else ''}{type(exc).__name__}: {exc}"
            if isinstance(exc, (KeyboardInterrupt, WaveInterrupted)):
                break
    signal.signal(signal.SIGTERM, prior_handler)
    # Derive the published peak with the same lifecycle reconstruction used by
    # validation. In particular, local replacement lanes are real processes
    # and must count alongside primaries and the shared helper.
    peak = _wave_record_concurrency(backend.events)
    record = {
        "schema_version": 2,
        "policy": POLICY_NAME,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "wave_index": index,
        "frozen_wave": list(ANNUAL_WAVES[index]),
        "requested_shards": list(shard_ids),
        "requested_concurrency": len(shard_ids),
        "maximum_observed_concurrency": peak,
        "classification": classification,
        "exception": error,
        "execution_context": dict(context),
        "authority": dict(authority),
        "worker_results": results,
        "returncodes": {key: 0 for key in results},
        "worker_root_pids": {},
        "worker_logs": {},
        "events": backend.events,
        "clock_anchor": backend.clock_anchor,
        "resource_triggers": [
            event
            for event in backend.events
            if event["kind"] in {"hard_rss_crossing", "memory_pressure"}
        ],
        "resource_samples": backend.samples,
        "peak_worker_rss_mib": {
            "solver_tree": max(
                (
                    max(cast(Sequence[float], item["worker_trees_mib"]), default=0.0)
                    for item in backend.samples
                ),
                default=0.0,
            )
        },
        "peak_aggregate_rss_mib": max(
            (float(cast(float, item["aggregate_mib"])) for item in backend.samples),
            default=0.0,
        ),
        "elapsed_critical_path_seconds": time.monotonic() - started,
        "phase_progress": {
            **artifact_ref(phase_root / "supervisor-progress.json"),
            "path": str(
                (phase_root / "supervisor-progress.json").relative_to(output_root)
            ),
        },
        "contender_lifecycles": {
            key: {
                **artifact_ref(child.directory / "lifecycle.json"),
                "path": str(
                    (child.directory / "lifecycle.json").relative_to(output_root)
                ),
            }
            for key, child in backend.children.items()
        },
    }
    validate_wave(record, output_root)
    path = output_root / f"supervision-wave-{index:03d}-{sequence:03d}.json"
    atomic_immutable_json(path, record)
    return {**record, "record_path": path.name, "record_sha256": sha256_path(path)}
