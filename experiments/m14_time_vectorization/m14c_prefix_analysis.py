"""Independently verify and summarize the supervised M14c prefix ladder."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Mapping, cast

import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.s4_analysis import (
    EXPECTED_EQUIVALENCE_DIMENSIONS,
    EXPECTED_EQUIVALENCE_INPUT_SHA256,
    _archived_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)
from experiments.m14_time_vectorization.m14c_prefix_fixture import (
    M14C_INTEGRATION_COMMIT,
    PRE_LADDER_INTEGRATION_CHECKPOINT,
    PRE_LADDER_INTEGRATION_SHA256,
    PREFIX_EXECUTION_LIMITS,
    PREFIX_EXPECTED_HASHES,
    PREFIX_LADDER_HORIZONS,
    PREFIX_LADDER_OUTPUT_DIRECTORY,
    load_prefix_fixture,
)
from experiments.m14_time_vectorization.run_m14c_prefix_ladder import (
    PREFIX_SOURCE_FILES,
    prefix_source_fingerprint,
)


SCHEMA_VERSION = 1
ANALYSIS_SOURCE_FILES = (
    *PREFIX_SOURCE_FILES,
    "experiments/m14_time_vectorization/m14c_prefix_analysis.py",
)
DEFAULT_DESTINATION = Path(
    "experiments/m14_time_vectorization/M14C_PREFIX_LADDER_RESULTS.json"
)
EXPECTED_PHASES = [
    "worker_start",
    "before_construction",
    "after_construction",
    "before_solve",
    "after_solve",
    "after_archive",
    "after_release",
]
FAILED_CLASSIFICATIONS = {
    "rss_limit",
    "worker_wall_limit",
    "total_wall_limit",
    "worker_launch_failure",
    "provenance_mismatch",
    "worker_failure",
    "construction_error",
    "solver_failure",
    "solver_certified_infeasible",
    "unusable_primal",
    "artifact_failure",
    "resource_measurement_failure",
    "residual_rejection",
    "worker_process_failure",
    "supervisor_interrupted",
    "supervisor_failure",
}
RESOURCE_CLASSIFICATIONS = ("rss_limit", "worker_wall_limit", "total_wall_limit")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def analysis_source_paths() -> tuple[Path, ...]:
    paths = [ROOT / name for name in ANALYSIS_SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    if any(not path.is_file() for path in result):
        raise FileNotFoundError("M14c prefix analysis source registry is incomplete")
    return result


def analysis_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in analysis_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def analysis_context() -> Mapping[str, object]:
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "analysis_source_fingerprint": analysis_source_fingerprint(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


def _validate_execution_context(context: Mapping[str, object]) -> None:
    """Validate historical execution provenance without matching this host."""
    annual = load_prefix_fixture(PREFIX_LADDER_HORIZONS[0]).annual
    commit = context.get("git_commit")
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or context.get("git_clean") is not True
        or context.get("source_fingerprint") != prefix_source_fingerprint(commit)
        or context.get("m14c_integration_commit") != M14C_INTEGRATION_COMMIT
        or context.get("m14c_integration_sha256") != PRE_LADDER_INTEGRATION_SHA256
        or context.get("generator_quadratic_cost") != annual.generator_quadratic_cost
        or context.get("generator_conditioning_evidence_sha256")
        != annual.generator_conditioning_evidence_sha256
        or context.get("m14c_integration_checkpoint")
        != PRE_LADDER_INTEGRATION_CHECKPOINT
        or context.get("m14c_source_commit") != annual.m14c_source_commit
        or context.get("big_experiment_parent_commit")
        != annual.big_experiment_parent_commit
        or context.get("m14c_merge_base_commit") != annual.m14c_merge_base_commit
        or context.get("prefix_ladder_executed") is not False
        or context.get("annual_execution_authorized") is not False
        or context.get("annual_scenario_sha256") != annual.scenario_hash
        or context.get("annual_component_hashes") != dict(annual.hashes)
        or context.get("policy_sha256") != annual.policy_sha256
        or context.get("solve_config_sha256") != annual.solve_config_sha256
        or context.get("temporal_assembly") != annual.temporal_assembly
        or context.get("canonicalization_backend") != annual.canonicalization_backend
        or not isinstance(context.get("software_versions"), Mapping)
        or not isinstance(context.get("platform"), str)
        or not isinstance(context.get("architecture"), str)
    ):
        raise ValueError("M14c historical execution provenance is invalid")
    if _git("merge-base", M14C_INTEGRATION_COMMIT, commit) != M14C_INTEGRATION_COMMIT:
        raise ValueError("M14c execution commit lacks integration ancestry")
    prefixes = _mapping(context.get("prefixes"), "M14c prefix registry")
    if set(prefixes) != {str(value) for value in PREFIX_LADDER_HORIZONS}:
        raise ValueError("M14c historical prefix registry is incomplete")
    for horizon in PREFIX_LADDER_HORIZONS:
        fixture = load_prefix_fixture(horizon)
        point = _mapping(prefixes.get(str(horizon)), "M14c prefix identity")
        expected_limits = {
            "rss_limit_mib": fixture.limits.child_rss_mib,
            "worker_wall_seconds": fixture.limits.worker_wall_seconds,
            "supervisor_wall_seconds": fixture.limits.supervisor_wall_seconds,
            "poll_seconds": fixture.limits.poll_seconds,
        }
        if point != {
            "horizon_steps": horizon,
            "delta_hours": fixture.inputs.delta,
            "prefix_scenario_sha256": PREFIX_EXPECTED_HASHES[horizon]["scenario"],
            "prefix_input_sha256": PREFIX_EXPECTED_HASHES[horizon]["input"],
            "resource_policy": expected_limits,
        }:
            raise ValueError("M14c historical prefix identity is invalid")


def _validate_point_context(
    point: Mapping[str, object], root: Mapping[str, object], horizon: int
) -> None:
    prefix = _mapping(
        _mapping(root.get("prefixes"), "M14c prefix registry").get(str(horizon)),
        "M14c prefix identity",
    )
    common = {
        key: value
        for key, value in root.items()
        if key not in {"schema_version", "prefixes"}
    }
    expected = {
        **common,
        **prefix,
    }
    if point != expected:
        raise ValueError("prefix execution context differs from retained root")


def _validate_equivalence(
    equivalence: Mapping[str, object], context: Mapping[str, object]
) -> None:
    first_fixture = load_prefix_fixture(PREFIX_LADDER_HORIZONS[0]).annual
    if (
        equivalence.get("equivalent") is not True
        or equivalence.get("horizon_steps") != 24
        or equivalence.get("mismatches") != []
        or equivalence.get("temporal_assembly") != first_fixture.temporal_assembly
        or equivalence.get("canonicalization_backend")
        != first_fixture.canonicalization_backend
        or equivalence.get("public_dimensions") != EXPECTED_EQUIVALENCE_DIMENSIONS
        or equivalence.get("streaming_dimensions") != EXPECTED_EQUIVALENCE_DIMENSIONS
        or equivalence.get("fingerprints_match") is not True
        or equivalence.get("public_fingerprints")
        != equivalence.get("streaming_fingerprints")
    ):
        raise ValueError("M14c retained S4 seam equivalence is not accepted")
    fingerprints = _mapping(
        equivalence.get("public_fingerprints"), "M14c equivalence fingerprints"
    )
    if (
        fingerprints.get("input_sha256") != EXPECTED_EQUIVALENCE_INPUT_SHA256
        or fingerprints.get("policy_sha256") != context.get("policy_sha256")
        or fingerprints.get("solve_config_sha256") != context.get("solve_config_sha256")
    ):
        raise ValueError("M14c equivalence provenance differs from ladder context")


def _accepted_prefix_summary(
    directory: Path,
    *,
    horizon_steps: int,
    root_context: Mapping[str, object],
) -> Mapping[str, object]:
    point_context = _mapping(
        json.loads((directory / "execution-context.json").read_text()),
        "prefix execution context",
    )
    _validate_point_context(point_context, root_context, horizon_steps)
    supervision_path = directory / "supervision.json"
    worker_path = directory / "worker-result.json"
    outer_path = directory / "outer-plan.json.gz"
    required = (
        directory / "execution-context.json",
        supervision_path,
        worker_path,
        directory / "worker.log",
        outer_path,
    )
    if any(not path.is_file() for path in required):
        raise ValueError("accepted prefix artifact set is incomplete")
    if (directory / "active-worker.json").exists():
        raise ValueError("accepted prefix worker remains marked active")
    supervision = _mapping(
        json.loads(supervision_path.read_text()), "prefix supervision"
    )
    worker = _mapping(json.loads(worker_path.read_text()), "prefix worker")
    limits = PREFIX_EXECUTION_LIMITS[horizon_steps]
    expected_policy = {
        "rss_limit_mib": limits.child_rss_mib,
        "worker_wall_seconds": limits.worker_wall_seconds,
        "supervisor_wall_seconds": limits.supervisor_wall_seconds,
        "poll_seconds": limits.poll_seconds,
    }
    worker_outer = _mapping(worker.get("outer_plan"), "prefix worker outer plan")
    worker_artifact = _mapping(
        worker_outer.get("artifact"), "prefix worker outer artifact"
    )
    fixture = load_prefix_fixture(horizon_steps)
    if (
        supervision.get("schema_version") != SCHEMA_VERSION
        or supervision.get("horizon_steps") != horizon_steps
        or supervision.get("classification") != "accepted"
        or supervision.get("returncode") != 0
        or supervision.get("launch_error") is not None
        or supervision.get("supervisor_interruption") is not None
        or supervision.get("resource_triggers") != []
        or supervision.get("context_matches") is not True
        or supervision.get("start_context") != point_context
        or supervision.get("end_context") != point_context
        or supervision.get("worker_result") != worker
        or supervision.get("resource_policy") != expected_policy
        or worker.get("classification") != "accepted"
        or worker.get("horizon_steps") != horizon_steps
        or worker.get("context_matches") is not True
        or worker.get("start_context") != point_context
        or worker.get("end_context") != point_context
        or worker_outer.get("accepted_primal") is not True
        or worker_outer.get("temporal_assembly") != fixture.annual.temporal_assembly
        or worker_outer.get("canonicalization_backend")
        != fixture.annual.canonicalization_backend
        or supervision.get("outer_plan_sha256") != sha256_path(outer_path)
        or worker_artifact.get("sha256") != sha256_path(outer_path)
        or worker_artifact.get("bytes") != outer_path.stat().st_size
        or supervision.get("worker_log_sha256") != sha256_path(directory / "worker.log")
    ):
        raise ValueError("prefix supervision provenance is not accepted")
    first_rss = _number(supervision.get("first_sampled_rss_mib"), "prefix first RSS")
    peak_rss = _number(supervision.get("peak_sampled_rss_mib"), "prefix peak RSS")
    if first_rss <= 0.0 or peak_rss <= 0.0 or peak_rss > limits.child_rss_mib:
        raise ValueError("prefix RSS evidence violates the frozen resource gate")
    worker_wall = _number(
        supervision.get("worker_wall_time_seconds"), "prefix worker wall time"
    )
    total_wall = _number(supervision.get("wall_time_seconds"), "prefix total wall")
    if (
        worker_wall > limits.worker_wall_seconds
        or total_wall > limits.supervisor_wall_seconds
    ):
        raise ValueError("accepted prefix exceeded a frozen wall-time limit")
    samples = cast(list[object], worker.get("resource_samples"))
    phases = [
        str(_mapping(sample, "prefix resource sample").get("phase"))
        for sample in samples
    ]
    if phases != EXPECTED_PHASES:
        raise ValueError("prefix worker phase evidence is incomplete or out of order")
    outer = load_verified_outer_plan_archive(
        outer_path,
        inputs=fixture.inputs,
        policy=fixture.annual.policy,
        expected_solve_config_sha256=fixture.annual.solve_config_sha256,
        expected_source_fingerprint=str(point_context["source_fingerprint"]),
        expected_scenario_hash=fixture.scenario_sha256,
    )
    dimensions = _mapping(worker.get("dimensions"), "prefix dimensions")
    if dimensions != _archived_dimensions(outer_path):
        raise ValueError("prefix structural counts do not reproduce from archive")
    terminal_target = np.asarray(
        [unit.terminal_soc for unit in fixture.inputs.storage], dtype=float
    )
    if outer.boundary_soc_mwh is None:
        raise ValueError("accepted prefix lacks SoC signposts")
    terminal_residual = float(
        np.max(np.abs(np.asarray(outer.boundary_soc_mwh[-1]) - terminal_target))
    )
    if terminal_residual > fixture.annual.policy.tolerances.terminal_soc_mwh_abs:
        raise ValueError("prefix terminal target fails independent reconstruction")
    return {
        "horizon_steps": horizon_steps,
        "classification": "accepted",
        "objective": _number(outer.result.get("objective"), "prefix objective"),
        "terminal_soc_residual_mwh_abs": terminal_residual,
        "audit_residuals": dict(outer.audit.residuals),
        "dimensions": dict(dimensions),
        "solve_wall_seconds": outer.wall_time_seconds,
        "worker_wall_seconds": worker_wall,
        "supervisor_wall_seconds": total_wall,
        "first_supervisor_rss_mib": first_rss,
        "peak_supervisor_rss_mib": peak_rss,
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in required
        },
    }


def _retained_worker_outcome(
    worker: Mapping[str, object],
    *,
    horizon_steps: int,
    point_context: Mapping[str, object],
) -> str:
    """Independently classify one retained worker payload."""
    if (
        worker.get("schema_version") != SCHEMA_VERSION
        or worker.get("horizon_steps") != horizon_steps
    ):
        raise ValueError("failed worker schema or horizon is inconsistent")
    declared = worker.get("classification")
    start_context = worker.get("start_context")
    if declared == "provenance_mismatch":
        has_end = "end_context" in worker
        has_match = "context_matches" in worker
        early_mismatch = (
            start_context != point_context and not has_end and not has_match
        )
        late_mismatch = (
            start_context == point_context
            and has_end
            and worker.get("end_context") != point_context
            and has_match
            and worker.get("context_matches") is False
        )
        if not (early_mismatch or late_mismatch):
            raise ValueError("worker provenance mismatch shape is inconsistent")
        return "provenance_mismatch"
    if (
        start_context != point_context
        or worker.get("end_context") != point_context
        or worker.get("context_matches") is not True
    ):
        raise ValueError("failed worker context chain is inconsistent")
    samples = worker.get("resource_samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("failed worker resource samples are invalid")
    phases = [
        str(_mapping(sample, "failed worker resource sample").get("phase"))
        for sample in samples
    ]
    if phases != EXPECTED_PHASES[: len(phases)]:
        raise ValueError("failed worker phase order is invalid")
    worker_exception = worker.get("exception")
    outer_raw = worker.get("outer_plan")
    outer = _mapping(outer_raw, "failed worker outer evidence") if outer_raw else None
    if worker_exception is not None:
        if not isinstance(worker_exception, str) or not worker_exception:
            raise ValueError("failed worker exception is invalid")
        if declared == "artifact_failure" and outer is not None:
            accepted_evidence = dict(worker)
            accepted_evidence["classification"] = "accepted"
            accepted_evidence["exception"] = None
            if (
                _retained_worker_outcome(
                    accepted_evidence,
                    horizon_steps=horizon_steps,
                    point_context=point_context,
                )
                != "accepted"
            ):
                raise ValueError("artifact failure lacks an accepted outer solve")
            derived = "artifact_failure"
        elif phases[-1] == "before_construction":
            derived = "construction_error"
        else:
            derived = "worker_failure"
    elif outer is None:
        derived = "worker_failure"
    else:
        outer_exception = outer.get("exception")
        status = outer.get("status")
        missing = outer.get("missing_or_nonfinite_fields")
        residuals_raw = outer.get("audit_residuals")
        if not isinstance(missing, list) or not all(
            isinstance(value, str) for value in missing
        ):
            raise ValueError("failed worker missing-field evidence is invalid")
        residuals = _mapping(residuals_raw, "failed worker audit residuals")
        numeric_residuals = {
            str(name): _number(value, f"failed residual {name}")
            for name, value in residuals.items()
        }
        if outer_exception is not None:
            if not isinstance(outer_exception, str) or not outer_exception:
                raise ValueError("failed outer exception is invalid")
            derived = "solver_failure"
        elif status in {"infeasible", "infeasible_inaccurate"}:
            derived = "solver_certified_infeasible"
        elif status not in {"optimal", "optimal_inaccurate"} or missing:
            derived = "unusable_primal"
        else:
            tolerances = load_prefix_fixture(horizon_steps).annual.policy.tolerances
            required = {
                "soc_recurrence_mwh_abs": tolerances.soc_recurrence_mwh_abs,
                "terminal_soc_mwh_abs": tolerances.terminal_soc_mwh_abs,
                "dc_injection_reporting_mw_abs": (
                    tolerances.dc_injection_reporting_mw_abs
                ),
                "dc_nodal_balance_pu_abs": tolerances.dc_nodal_balance_pu_abs,
                "branch_mw_abs": 1e-4,
            }
            residual_gates_pass = set(required) <= set(numeric_residuals) and all(
                numeric_residuals[name] <= limit for name, limit in required.items()
            )
            identity_error = outer.get("identity_error")
            accepted = outer.get("accepted_primal")
            expected_accepted = identity_error is None and residual_gates_pass
            if not isinstance(accepted, bool) or accepted is not expected_accepted:
                raise ValueError(
                    "worker accepted flag contradicts retained audit gates"
                )
            derived = "accepted" if expected_accepted else "residual_rejection"
    if declared != derived:
        raise ValueError("worker classification contradicts retained evidence")
    return derived


def _failed_prefix_summary(
    directory: Path,
    *,
    horizon_steps: int,
    classification: str,
    root_context: Mapping[str, object],
) -> Mapping[str, object]:
    """Validate one retained nonaccepted terminal prefix without reclassifying it."""
    if classification not in FAILED_CLASSIFICATIONS:
        raise ValueError("prefix failure classification is not frozen")
    context_path = directory / "execution-context.json"
    supervision_path = directory / "supervision.json"
    log_path = directory / "worker.log"
    if any(not path.is_file() for path in (context_path, supervision_path, log_path)):
        raise ValueError("failed prefix artifact set is incomplete")
    if (directory / "active-worker.json").exists():
        raise ValueError("failed prefix worker remains marked active")
    point_context = _mapping(
        json.loads(context_path.read_text()), "failed prefix execution context"
    )
    _validate_point_context(point_context, root_context, horizon_steps)
    supervision = _mapping(
        json.loads(supervision_path.read_text()), "failed prefix supervision"
    )
    limits = PREFIX_EXECUTION_LIMITS[horizon_steps]
    expected_policy = {
        "rss_limit_mib": limits.child_rss_mib,
        "worker_wall_seconds": limits.worker_wall_seconds,
        "supervisor_wall_seconds": limits.supervisor_wall_seconds,
        "poll_seconds": limits.poll_seconds,
    }
    end_context = supervision.get("end_context")
    context_matches = point_context == end_context
    worker_path = directory / "worker-result.json"
    worker = (
        _mapping(json.loads(worker_path.read_text()), "failed prefix worker")
        if worker_path.is_file()
        else None
    )
    if (
        supervision.get("schema_version") != SCHEMA_VERSION
        or supervision.get("horizon_steps") != horizon_steps
        or supervision.get("classification") != classification
        or supervision.get("start_context") != point_context
        or supervision.get("context_matches") is not context_matches
        or supervision.get("resource_policy") != expected_policy
        or supervision.get("worker_result") != worker
        or supervision.get("worker_log_sha256") != sha256_path(log_path)
    ):
        raise ValueError("failed prefix supervision provenance is inconsistent")
    resource_triggers = supervision.get("resource_triggers")
    if not isinstance(resource_triggers, list):
        raise ValueError("failed prefix resource-trigger registry is invalid")
    if len(set(resource_triggers)) != len(resource_triggers) or resource_triggers != [
        value for value in RESOURCE_CLASSIFICATIONS if value in resource_triggers
    ]:
        raise ValueError("failed prefix resource-trigger priority is invalid")
    if resource_triggers and classification != resource_triggers[0]:
        raise ValueError("failed prefix classification differs from first trigger")
    peak_rss = supervision.get("peak_sampled_rss_mib")
    worker_wall = _number(
        supervision.get("worker_wall_time_seconds"), "failed worker wall time"
    )
    total_wall = _number(
        supervision.get("wall_time_seconds"), "failed supervisor wall time"
    )
    if "rss_limit" in resource_triggers and (
        _number(peak_rss, "failed prefix peak RSS") <= limits.child_rss_mib
    ):
        raise ValueError("RSS trigger did not cross its frozen threshold")
    if (
        "worker_wall_limit" in resource_triggers
        and worker_wall <= limits.worker_wall_seconds
    ):
        raise ValueError("worker-wall trigger did not cross its frozen threshold")
    if (
        "total_wall_limit" in resource_triggers
        and total_wall <= limits.supervisor_wall_seconds
    ):
        raise ValueError("total-wall trigger did not cross its frozen threshold")
    launch_error = supervision.get("launch_error")
    supervisor_error = supervision.get("supervisor_error")
    interrupted = supervision.get("supervisor_interruption")
    returncode = supervision.get("returncode")
    worker_outcome = (
        None
        if worker is None
        else _retained_worker_outcome(
            worker, horizon_steps=horizon_steps, point_context=point_context
        )
    )
    if launch_error is not None and (
        not isinstance(launch_error, str) or not launch_error
    ):
        raise ValueError("worker launch error evidence is invalid")
    if launch_error is not None and worker is not None:
        raise ValueError("worker launch failure cannot retain a worker payload")
    if interrupted is not None and (
        not isinstance(interrupted, str) or not interrupted
    ):
        raise ValueError("supervisor interruption evidence is invalid")
    if supervisor_error is not None and (
        not isinstance(supervisor_error, str) or not supervisor_error
    ):
        raise ValueError("supervisor failure evidence is invalid")
    if launch_error is not None:
        expected_classification = "worker_launch_failure"
    elif resource_triggers:
        expected_classification = str(resource_triggers[0])
    elif interrupted is not None:
        expected_classification = "supervisor_interrupted"
    elif supervisor_error is not None:
        expected_classification = "supervisor_failure"
    elif not context_matches or worker_outcome == "provenance_mismatch":
        expected_classification = "provenance_mismatch"
    elif returncode != 0:
        expected_classification = (
            worker_outcome
            if worker_outcome not in {None, "accepted"}
            else "worker_process_failure"
        )
    elif worker is None:
        expected_classification = "worker_failure"
    else:
        expected_classification = str(worker_outcome)
    artifact_paths = [context_path, supervision_path, log_path]
    if worker_path.is_file():
        artifact_paths.append(worker_path)
    outer_path = directory / "outer-plan.json.gz"
    worker_artifact_matches = False
    if outer_path.is_file():
        if supervision.get("outer_plan_sha256") != sha256_path(outer_path):
            raise ValueError("failed prefix outer artifact hash is inconsistent")
        if worker is not None:
            outer_payload = worker.get("outer_plan")
            if isinstance(outer_payload, Mapping):
                artifact = outer_payload.get("artifact")
                worker_artifact_matches = isinstance(artifact, Mapping) and (
                    artifact.get("sha256") == sha256_path(outer_path)
                    and artifact.get("bytes") == outer_path.stat().st_size
                )
            if worker_outcome == "accepted" and worker_artifact_matches:
                fixture = load_prefix_fixture(horizon_steps)
                load_verified_outer_plan_archive(
                    outer_path,
                    inputs=fixture.inputs,
                    policy=fixture.annual.policy,
                    expected_solve_config_sha256=fixture.annual.solve_config_sha256,
                    expected_source_fingerprint=str(
                        point_context["source_fingerprint"]
                    ),
                    expected_scenario_hash=fixture.scenario_sha256,
                )
        artifact_paths.append(outer_path)
    elif supervision.get("outer_plan_sha256") is not None:
        raise ValueError("failed prefix names a missing outer artifact")
    outer_valid = False
    if worker_outcome == "accepted" and outer_path.is_file() and worker is not None:
        outer_valid = worker_artifact_matches
    if expected_classification == "accepted":
        if not outer_valid:
            expected_classification = "artifact_failure"
        else:
            first_rss = supervision.get("first_sampled_rss_mib")
            peak_valid = (
                isinstance(peak_rss, (int, float))
                and not isinstance(peak_rss, bool)
                and np.isfinite(peak_rss)
                and peak_rss > 0.0
            )
            if first_rss is None or not peak_valid:
                expected_classification = "resource_measurement_failure"
    if classification != expected_classification:
        raise ValueError(
            "failed prefix classification differs from reconstructed outcome"
        )
    return {
        "horizon_steps": horizon_steps,
        "classification": classification,
        "returncode": returncode,
        "resource_triggers": resource_triggers,
        "first_supervisor_rss_mib": supervision.get("first_sampled_rss_mib"),
        "peak_supervisor_rss_mib": supervision.get("peak_sampled_rss_mib"),
        "worker_wall_seconds": supervision.get("worker_wall_time_seconds"),
        "supervisor_wall_seconds": supervision.get("wall_time_seconds"),
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in artifact_paths
        },
    }


def analyze_prefix_ladder(
    directory: Path = PREFIX_LADDER_OUTPUT_DIRECTORY,
) -> Mapping[str, object]:
    """Validate the ordered lifecycle and independently reconstruct accepted points."""
    directory = directory.expanduser().resolve()
    context_path = directory / "execution-context.json"
    equivalence_path = directory / "outer-equivalence.json"
    result_path = directory / "ladder-result.json"
    progress_path = directory / "ladder-progress.json"
    if any(not path.is_file() for path in (context_path, equivalence_path)) or not (
        result_path.is_file() or progress_path.is_file()
    ):
        raise ValueError("M14c prefix-ladder root artifact set is incomplete")
    context = _mapping(json.loads(context_path.read_text()), "ladder context")
    if context.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("M14c ladder execution schema is invalid")
    _validate_execution_context(context)
    equivalence = _mapping(
        json.loads(equivalence_path.read_text()), "ladder S4 equivalence"
    )
    _validate_equivalence(equivalence, context)
    finalized = result_path.is_file()
    ladder = _mapping(
        json.loads((result_path if finalized else progress_path).read_text()),
        "ladder result" if finalized else "ladder progress",
    )
    records = cast(list[object], ladder.get("records"))
    attempted = cast(list[int], ladder.get("attempted_horizons"))
    accepted = cast(list[int], ladder.get("accepted_horizons"))
    if (
        ladder.get("schema_version") != SCHEMA_VERSION
        or attempted != list(PREFIX_LADDER_HORIZONS[: len(attempted)])
        or accepted != attempted[: len(accepted)]
        or len(records) != len(attempted)
        or ladder.get("execution_context_sha256") != sha256_path(context_path)
        or ladder.get("outer_equivalence_sha256") != sha256_path(equivalence_path)
        or ladder.get("annual_execution_authorized") is not False
    ):
        raise ValueError("M14c ordered ladder registry is inconsistent")
    if finalized:
        if not progress_path.is_file() or ladder.get(
            "ladder_progress_sha256"
        ) != sha256_path(progress_path):
            raise ValueError("M14c finalized ladder lacks bound progress evidence")
        progress = _mapping(json.loads(progress_path.read_text()), "ladder progress")
        for key in (
            "attempted_horizons",
            "accepted_horizons",
            "stopped_horizon",
            "interrupted_horizon",
            "records",
            "execution_context_sha256",
            "outer_equivalence_sha256",
            "annual_execution_authorized",
        ):
            if progress.get(key) != ladder.get(key):
                raise ValueError("M14c root progress differs from final lifecycle")
    summaries: list[Mapping[str, object]] = []
    observed_classifications: list[str] = []
    for index, item in enumerate(records):
        record = _mapping(item, "ladder record")
        horizon_steps = attempted[index]
        point_directory = directory / f"prefix-{horizon_steps:04d}"
        supervision_path = point_directory / "supervision.json"
        if (
            record.get("horizon_steps") != horizon_steps
            or record.get("directory") != point_directory.name
            or not supervision_path.is_file()
            or record.get("supervision_sha256") != sha256_path(supervision_path)
        ):
            raise ValueError("M14c ladder record identity mismatch")
        classification = str(record.get("classification"))
        observed_classifications.append(classification)
        if classification == "accepted":
            summaries.append(
                _accepted_prefix_summary(
                    point_directory,
                    horizon_steps=horizon_steps,
                    root_context=context,
                )
            )
        else:
            summaries.append(
                _failed_prefix_summary(
                    point_directory,
                    horizon_steps=horizon_steps,
                    classification=classification,
                    root_context=context,
                )
            )
    actual_accepted = [
        horizon
        for horizon, classification in zip(
            attempted, observed_classifications, strict=True
        )
        if classification == "accepted"
    ]
    if accepted != actual_accepted:
        raise ValueError("M14c accepted-horizon registry contradicts supervision")
    first_failure = next(
        (
            index
            for index, value in enumerate(observed_classifications)
            if value != "accepted"
        ),
        None,
    )
    if first_failure is not None and first_failure != len(records) - 1:
        raise ValueError("M14c ladder continued after a nonaccepted prefix")
    complete = finalized and (
        attempted == list(PREFIX_LADDER_HORIZONS)
        and accepted == list(PREFIX_LADDER_HORIZONS)
        and observed_classifications == ["accepted"] * len(PREFIX_LADDER_HORIZONS)
    )
    if finalized:
        expected_terminal = (
            "accepted" if complete else str(ladder.get("classification"))
        )
        expected_stopped = None if complete else ladder.get("stopped_horizon")
        if complete:
            if expected_terminal != "accepted":
                raise ValueError("M14c terminal ladder classification is inconsistent")
        elif expected_terminal == "supervisor_interrupted":
            interrupted = ladder.get("interrupted_horizon")
            if (
                interrupted not in PREFIX_LADDER_HORIZONS
                or interrupted != expected_stopped
            ):
                raise ValueError("M14c interrupted horizon is inconsistent")
        elif expected_terminal != "stopped":
            raise ValueError("M14c terminal ladder classification is inconsistent")
        if (
            ladder.get("execution_complete") is not complete
            or ladder.get("stopped_horizon")
            != (
                None
                if complete
                else ladder.get("interrupted_horizon")
                if expected_terminal == "supervisor_interrupted"
                else attempted[-1]
                if attempted
                else None
            )
            or ladder.get("interrupted_horizon")
            != (
                ladder.get("stopped_horizon")
                if expected_terminal == "supervisor_interrupted"
                else None
            )
        ):
            raise ValueError("M14c terminal ladder classification is inconsistent")
    elif ladder.get("classification") not in {
        "accepted",
        "running",
        "stopped",
        "supervisor_interrupted",
    }:
        raise ValueError("M14c unfinalized progress classification is invalid")
    root_paths = [context_path, equivalence_path, progress_path]
    if finalized:
        root_paths.append(result_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_complete": complete,
        "classification": "accepted" if complete else "partial",
        "lifecycle_finalized": finalized,
        "terminal_classification": ladder.get("classification"),
        "stopped_horizon": ladder.get("stopped_horizon"),
        "interrupted_horizon": ladder.get("interrupted_horizon"),
        "qualified_for_annual_review": complete,
        "annual_execution_authorized": False,
        "attempted_horizons": attempted,
        "accepted_horizons": accepted,
        "prefixes": summaries,
        "execution_context": context,
        "analysis_context": analysis_context(),
        "outer_equivalence": equivalence,
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in root_paths
        },
    }


def promote_prefix_ladder(
    result: Mapping[str, object],
    destination: Path,
    *,
    source_directory: Path = PREFIX_LADDER_OUTPUT_DIRECTORY,
) -> None:
    """Immutably promote only a complete independently accepted ladder record."""
    required_keys = {
        "schema_version",
        "execution_complete",
        "classification",
        "lifecycle_finalized",
        "terminal_classification",
        "stopped_horizon",
        "interrupted_horizon",
        "qualified_for_annual_review",
        "annual_execution_authorized",
        "attempted_horizons",
        "accepted_horizons",
        "prefixes",
        "execution_context",
        "analysis_context",
        "outer_equivalence",
        "artifacts",
    }
    if (
        set(result) != required_keys
        or result.get("schema_version") != SCHEMA_VERSION
        or result.get("execution_complete") is not True
        or result.get("classification") != "accepted"
        or result.get("lifecycle_finalized") is not True
        or result.get("terminal_classification") != "accepted"
        or result.get("stopped_horizon") is not None
        or result.get("interrupted_horizon") is not None
        or result.get("qualified_for_annual_review") is not True
        or result.get("annual_execution_authorized") is not False
        or result.get("attempted_horizons") != list(PREFIX_LADDER_HORIZONS)
        or result.get("accepted_horizons") != list(PREFIX_LADDER_HORIZONS)
        or not isinstance(result.get("prefixes"), list)
        or len(cast(list[object], result.get("prefixes")))
        != len(PREFIX_LADDER_HORIZONS)
    ):
        raise ValueError("only a complete prefix ladder may be promoted for review")
    reconstructed = analyze_prefix_ladder(source_directory)
    if result != reconstructed:
        raise ValueError("promotion input differs from independent reconstruction")
    current = analysis_context()
    if (
        result.get("analysis_context") != current
        or current.get("git_clean") is not True
    ):
        raise ValueError("prefix promotion requires a clean matching analyzer context")
    atomic_immutable_json(destination, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=PREFIX_LADDER_OUTPUT_DIRECTORY
    )
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    arguments = parser.parse_args()
    result = analyze_prefix_ladder(arguments.directory)
    if arguments.promote:
        promote_prefix_ladder(
            result, arguments.destination, source_directory=arguments.directory
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
